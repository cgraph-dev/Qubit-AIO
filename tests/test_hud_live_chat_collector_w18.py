"""Live Chat collector — the in-flight snapshot, read against a real journal.

``voicebridge/hud/collectors_live_chat.py`` is the HUD panel with the least test
coverage of the nineteen (it is the one panel whose upstream counterpart is a
WebSocket that this build refuses, so it had to be re-derived rather than
ported). Nothing exercised its logic: not the run-open/run-closed accounting,
not the stage snapshot join, not the redacted tail, not the bounded sizes.

The panel answers a polled snapshot of the ONE durable event path
(``EventJournal`` over ``<state_dir>/typed-events.jsonl``). Its contract, all of
which is asserted here against events written through the real journal:

1. **Active runs are started-minus-finished.** ``RUN_FINISHED``,
   ``RUN_CANCELLED`` and ``RUN_ERROR`` all close a run; a run that only started
   stays open. A panel that counted a cancelled run as active would claim work
   in flight that is not.
2. **The stage join is real.** A ``STATE_SNAPSHOT`` correlated to a run puts
   that run's own stage/description on its active row — and the LAST snapshot
   wins, because the panel is a snapshot of now, not a history.
3. **The tail is verbatim, redacted and bounded.** ``TAIL`` events, oldest of
   the window first, each with a typed ``type`` and no more than the collector's
   own preview cap.
4. **Sizes are bounded.** ``MAX_RUNS`` caps the active list even when more runs
   started than that.
5. **Absence is typed.** No journal file is ``available: True`` with the reason
   named, and the ``WS_SINGLE_PATH`` refusal travels in every response.

Hermetic: a tmp ``state_dir``, the real ``EventJournal`` writer, no network.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from voicebridge import config as C
from voicebridge.events.journal import EventJournal
from voicebridge.hud import collectors_live_chat as LC


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    """A VoiceBridge config whose ``state_dir`` is a tmp dir."""
    for key, value in {
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
        "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
        "QUBIT_HOME": str(tmp_path / "qhome"),
        "HERMES_HOME": str(tmp_path / "qhome"),
        "HOME": str(tmp_path / "home"),
    }.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "qhome").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    config_file = tmp_path / "voice.yaml"
    config_file.write_text("logging:\n  jsonl: false\n", encoding="utf-8")
    return C.Config.load(config_file)


def _journal(cfg) -> EventJournal:
    return EventJournal(Path(cfg.state_dir()) / "typed-events.jsonl",
                        fsync=False)


def test_absent_journal_is_typed_not_silent(cfg):
    """No journal yet: available, with the reason, and the WS refusal stated."""
    out = LC.collect_live_chat(cfg=cfg)
    assert out["available"] is True
    assert out["path_present"] is False
    assert out["records"] == 0 and out["tail"] == []
    assert "not been written yet" in out["detail"]
    assert out["transport"]["code"] == "WS_SINGLE_PATH"
    assert out["active_runs"] == [] and out["last_snapshot"] == {}


def test_active_runs_are_started_minus_finished(cfg):
    """A finished / cancelled / errored run stops being active; an open one stays."""
    journal = _journal(cfg)
    for run_id in ("open-1", "done-1", "cancelled-1", "errored-1"):
        journal.append("RUN_STARTED", run_id=run_id, thread_id=f"th-{run_id}")
    journal.append("RUN_FINISHED", run_id="done-1", thread_id="th-done-1")
    journal.append("RUN_CANCELLED", run_id="cancelled-1",
                   thread_id="th-cancelled-1")
    # RUN_ERROR has no run field in its pinned shape; ``RunCoordinator.fail``
    # correlates it through the journal envelope, which lands as ``run_id``.
    journal.append("RUN_ERROR", message="boom",
                   correlation_run_id="errored-1")

    out = LC.collect_live_chat(cfg=cfg)
    assert out["available"] is True
    active = [row["run_id"] for row in out["active_runs"]]
    assert active == ["open-1"], active
    assert out["records"] == 7, out["records"]
    assert "1 run(s) started without a matching finish event" in out["detail"]


def test_stage_snapshot_joins_the_active_run_and_last_one_wins(cfg):
    """The active row carries its own latest stage, never a sibling's."""
    journal = _journal(cfg)
    journal.append("RUN_STARTED", run_id="run-a", thread_id="th-a")
    journal.append("RUN_STARTED", run_id="run-b", thread_id="th-b")
    journal.append("STATE_SNAPSHOT", correlation_run_id="run-a",
                   snapshot={"stage": "plan", "description": "first thought"})
    journal.append("STATE_SNAPSHOT", correlation_run_id="run-b",
                   snapshot={"stage": "tools", "description": "b is busy"})
    journal.append("STATE_SNAPSHOT", correlation_run_id="run-a",
                   snapshot={"stage": "verify", "description": "a is checking"})

    out = LC.collect_live_chat(cfg=cfg)
    by_run = {row["run_id"]: row for row in out["active_runs"]}
    assert by_run["run-a"]["stage"] == "verify", by_run["run-a"]
    assert by_run["run-a"]["description"] == "a is checking"
    # A's stage never leaks onto B's row.
    assert by_run["run-b"]["stage"] == "tools", by_run["run-b"]
    # ``last_snapshot`` is the newest one recorded, whichever run it belongs to.
    assert out["last_snapshot"]["run_id"] == "run-a"
    assert out["last_snapshot"]["stage"] == "verify"


def test_tail_is_ordered_bounded_and_preview_capped(cfg):
    """The tail is the last TAIL events in journal order, previews truncated."""
    journal = _journal(cfg)
    # CUSTOM events close the window (they are the last events in the journal),
    # and the 900-char delta sits inside it so its preview must be capped.
    total = 0
    for i in range(LC.TAIL - 2):
        journal.append("CUSTOM", name=f"tick-{i}", value=i)
        total += 1
    journal.append("TEXT_MESSAGE_START", message_id="m1", role="assistant")
    journal.append("TEXT_MESSAGE_CONTENT", message_id="m1", delta="x" * 900)
    total += 2

    out = LC.collect_live_chat(cfg=cfg)
    assert out["records"] == total, out["records"]
    assert out["tail_count"] == LC.TAIL, out["tail_count"]
    assert len(out["tail"]) == LC.TAIL
    # The window is the END of the journal, and order is journal order.
    assert out["tail"][-1]["type"] == "TEXT_MESSAGE_CONTENT", out["tail"][-1]
    assert out["tail"][0]["seq"] < out["tail"][-1]["seq"]
    # Every row carries a typed event name; the delta preview is capped.
    assert all(row["type"] for row in out["tail"])
    previews = [row["detail"] for row in out["tail"] if row.get("detail")]
    assert previews, "the TEXT_MESSAGE_CONTENT delta must reach the tail"
    assert all(len(str(p)) <= 300 for p in previews), max(map(len, previews))
    # A window that starts past the head of the journal is still bounded.
    journal.append("CUSTOM", name="after", value=1)
    later = LC.collect_live_chat(cfg=cfg)
    assert later["records"] == total + 1, later["records"]
    assert later["tail"][-1]["type"] == "CUSTOM", later["tail"][-1]
    assert len(later["tail"]) == LC.TAIL


def test_active_run_list_is_bounded(cfg):
    """More simultaneous starts than MAX_RUNS still answers a bounded list."""
    journal = _journal(cfg)
    for i in range(LC.MAX_RUNS + 6):
        journal.append("RUN_STARTED", run_id=f"run-{i}", thread_id=f"th-{i}")

    out = LC.collect_live_chat(cfg=cfg)
    assert len(out["active_runs"]) == LC.MAX_RUNS, len(out["active_runs"])
    # Newest first: the list is reversed, so the highest id leads.
    assert out["active_runs"][0]["run_id"] == f"run-{LC.MAX_RUNS + 5}"
    assert f"({LC.MAX_RUNS + 6} run(s) recorded)" in out["detail"]


def test_unreadable_journal_is_a_typed_refusal(cfg, monkeypatch):
    """A journal that raises on read is reported, never propagated.

    The collector's contract is "never raises": one corrupt store must not take
    the dashboard's request thread down with it.
    """
    journal = _journal(cfg)
    journal.append("RUN_STARTED", run_id="run-a", thread_id="th-a")

    def _boom(self):
        raise OSError("journal unreadable")

    monkeypatch.setattr(EventJournal, "read_all", _boom, raising=True)
    out = LC.collect_live_chat(cfg=cfg)
    assert out["available"] is False
    assert "event journal unreadable" in out["detail"], out["detail"]
    assert out["tail"] == [] and out["active_runs"] == []
    # The refusal names the one event path rather than inventing a second.
    assert out["transport"]["code"] == "WS_SINGLE_PATH"


def test_coordinator_terminals_all_close_the_run_in_the_panel(cfg):
    """The REAL producer: start -> terminal, for every terminal the coordinator owns.

    This drives ``RunCoordinator`` (which ``events/runs.py`` calls the one
    emitter of RUN_* events) rather than hand-writing journal rows, so a change
    to how a terminal is correlated shows up here as a panel that still claims
    the run is in flight.
    """
    from voicebridge.events.hub import EventHub
    from voicebridge.events.runs import RunCoordinator

    path = Path(cfg.state_dir()) / "typed-events.jsonl"
    coordinator = RunCoordinator(EventHub(EventJournal(path, fsync=False)))

    coordinator.start("run-finish", "th-finish")
    coordinator.finish("run-finish", result={"ok": True})
    coordinator.start("run-fail", "th-fail")
    coordinator.fail("run-fail", "provider exploded")
    coordinator.start("run-open", "th-open")

    out = LC.collect_live_chat(cfg=cfg)
    active = [row["run_id"] for row in out["active_runs"]]
    assert active == ["run-open"], active
    assert out["records"] >= 5, out["records"]
