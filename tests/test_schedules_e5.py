"""E5 — the ONE scheduler routes user ``automation:`` directives.

The scheduled-job store/CLI/dashboard/voice already exist; these tests prove
that a job whose task text is an ``automation:`` directive is dispatched by the
production ``schedules.run_due`` path (the same caller the monitor
``autofire`` uses) to ``automation.run_directive`` — never to the agent
executor — and that a directive failure is a typed per-job receipt that cannot
block later jobs.

Hermetic: isolated XDG/state, injected executors, no network/model.
"""
from __future__ import annotations

import time

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_directive_bypasses_injected_executor(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    seen = []
    SC.create(cfg, "directive", "automation: heartbeat.add name=hb every=5",
              "every 5 minutes", confirmed=True)
    out = SC.run_due(cfg, executor=lambda *a, **k: seen.append(a),
                     now=time.time() + 3600)
    assert seen == [], "the agent executor must not run a directive"
    assert out["ran"] == 1 and out["results"][0]["state"] == "OK"


def test_non_directive_still_uses_executor(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "agent", "summarize the inbox", "every 5 minutes",
              confirmed=True)
    out = SC.run_due(cfg, executor=lambda *a, **k: {"state": "OK", "code": "X"},
                     now=time.time() + 3600)
    assert out["ran"] == 1 and out["results"][0]["state"] == "OK"


def test_directive_failure_is_typed_and_nonblocking(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "bad", "automation: not.a.verb", "every 5 minutes",
              confirmed=True)
    SC.create(cfg, "good", "automation: status", "every 5 minutes",
              confirmed=True)
    out = SC.run_due(cfg, now=time.time() + 3600)
    states = {r["name"]: r["state"] for r in out["results"]}
    assert states["bad"] == "FAILED"
    assert states["good"] == "OK"


def test_autofire_routes_directive_with_monitor_actor(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "directive", "automation: heartbeat.add name=hb every=5",
              "every 5 minutes", confirmed=True)
    out = SC.autofire(cfg, now=time.time() + 3600)
    assert out["actor"] == SC.MONITOR_ACTOR
    assert out["results"][0]["state"] == "OK"


def test_directive_estop_denies_whole_tick(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    SC.create(cfg, "directive", "automation: status", "every 5 minutes",
              confirmed=True)
    out = SC.autofire(cfg, now=time.time() + 3600,
                      estop_engaged=lambda: True)
    assert out["state"] == "denied" and out["ran"] == 0


def test_create_requires_confirm_before_dispatch(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(SC.SchedulesError) as nc:
        SC.create(cfg, "directive", "automation: heartbeat.add name=hb every=5",
                  "every 5 minutes", confirmed=False)
    assert nc.value.code == SC.CONFIRM_CODE
    assert SC.list_jobs(cfg)["jobs"] == []
