"""Focused regressions for the C13 cron-soak harness.

Two harness facts the soak relies on, pinned without running the 30-virtual-day scenarios:

* delivery attribution: a gateway drain sends off the run's thread (no contextvar) and a reused
  thread may still hold a finished run's id, so the ``exec=<id>`` stamp in the message body is the
  authoritative attribution; the contextvar is only a fallback for a tokenless body;
* extra fires: a trigger/manual run never consumes the repeat budget and never re-stamps a still
  pending occurrence (``cron/extra_fire.py``); only a slot already due is dropped and re-anchored.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("croniter")

from tests.e2e.core.delivery import _cron_clock as H  # noqa: E402
from tests.e2e.core.delivery import test_cron_virtual_clock_soak as S  # noqa: E402

EXEC_A = "a" * 32
EXEC_B = "b" * 32


def _harness(tmp_path):
    control = H.Control(tmp_path / "control").ensure()
    clock = H.VirtualClock(control.clock_file)
    clock.set(1000.0)
    return control, clock


def _sink_rows(control):
    return H.read_jsonl(control.sink)


def test_fake_sink_prefers_the_body_exec_token_over_a_stale_contextvar(tmp_path):
    """A reused thread still carrying a finished run's id must not mis-attribute the send."""
    control, clock = _harness(tmp_path)
    sink = H.FakeSink(clock, control)
    token = H._CURRENT_EXECUTION.set(EXEC_A)
    try:
        asyncio.run(sink(object(), {}, "1002", f"report exec={EXEC_B}"))
    finally:
        H._CURRENT_EXECUTION.reset(token)
    rows = _sink_rows(control)
    assert [r["exec"] for r in rows] == [EXEC_B]
    assert rows[0]["exec_in_text"] is True


def test_fake_sink_attributes_a_tokenless_body_to_the_contextvar(tmp_path):
    control, clock = _harness(tmp_path)
    sink = H.FakeSink(clock, control)
    token = H._CURRENT_EXECUTION.set(EXEC_A)
    try:
        asyncio.run(sink(object(), {}, "1001", "no token in this body"))
    finally:
        H._CURRENT_EXECUTION.reset(token)
    rows = _sink_rows(control)
    assert [r["exec"] for r in rows] == [EXEC_A]
    assert rows[0]["exec_in_text"] is False


def test_fake_agent_does_not_leak_its_execution_into_a_later_send(tmp_path):
    control, clock = _harness(tmp_path)
    agent = H.FakeAgent(clock, control)
    assert H._CURRENT_EXECUTION.get() is None
    ok, output, message, error = agent({"name": "brief", "id": "job-1"}, execution_id=EXEC_A)
    assert ok and error is None and f"exec={EXEC_A}" in output
    assert f"exec={EXEC_A}" in message
    assert H._CURRENT_EXECUTION.get() is None  # reset, not left for the next send


def test_extra_fire_keeps_a_still_future_occurrence_and_the_repeat_budget():
    """A trigger before the next slot must not spend it: the product restores it at claim time."""
    model = S.Model()
    job = S.ModelJob("every-2h", "interval", pending=7200.0, interval=7200.0, times=5)
    model.jobs[job.name] = job
    model.manual(job.name, t=1000.0)
    assert job.fired == 0, "an extra fire must not consume the repeat budget"
    assert job.pending == 7200.0, "a still-future occurrence must survive the extra fire"
    assert model.fires == [(job.name, None, 1000.0)]


def test_extra_fire_reanchors_only_a_slot_already_due():
    model = S.Model()
    job = S.ModelJob("every-2h", "interval", pending=7200.0, interval=7200.0, times=5)
    model.jobs[job.name] = job
    model.manual(job.name, t=8000.0)  # the pending slot (7200) already passed
    assert job.pending == 8000.0 + 7200.0
    assert job.fired == 0


def test_extra_fire_keeps_a_pending_one_shot():
    model = S.Model()
    job = S.ModelJob("once", "once", pending=7200.0, times=1)
    model.jobs[job.name] = job
    model.manual(job.name, t=1000.0)
    assert job.pending == 7200.0  # the real reminder still fires
