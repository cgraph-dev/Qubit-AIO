"""Hermetic pins for the LIVE orb capture harness (``scripts/capture_orb_live.py``).

The live part (X11 grab + real journal tail) cannot be asserted in CI: there is
no window and the journal is owner data. What CAN be pinned — and is the part a
refactor would silently break — is the parsing and timeline logic:

  * ``classify_event`` keeps ``voice_edit.talk.*`` boundaries and the stop
    markers (``voice_edit.cancel`` / ``stt.cancelled``) and drops everything else;
  * ``build_timeline`` orders a stop press as ``interrupt`` -> ``playback.stopped``
    -> ``response.done`` with correct relative times and turn ids;
  * ``run`` writes ``frame-NNN.png`` + ``timeline.txt`` + ``events.json``, counts
    events, includes pre-window context, and degrades cleanly (no traceback, a
    ``degraded`` note, timeline still written) when there is no window or journal;
  * the synthetic press helper is fail-closed unless BOTH the env gate and the
    explicit flag are set.

The harness module is loaded by path so nothing under ``scripts/`` needs a
package ``__init__`` and NO product code is imported for the pure cases.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "capture_orb_live.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "capture_orb_live_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    assert SCRIPT_PATH.is_file(), f"harness missing: {SCRIPT_PATH}"
    return _load_harness()


# --------------------------------------------------------------------------- #
# classify_event
# --------------------------------------------------------------------------- #
def test_classify_talk_boundary(harness):
    ev = harness.classify_event({
        "ts": 100.5, "event": "voice_edit.talk.interrupt",
        "turn_id": "turn-7", "seq": 42, "pid": 1234,
    })
    assert ev == {
        "ts": 100.5, "event": "voice_edit.talk.interrupt", "kind": "talk",
        "type": "interrupt", "turn_id": "turn-7", "seq": 42, "pid": 1234,
    }


@pytest.mark.parametrize("name", [
    "voice_edit.cancel",
    "qubit_compat.voice_edit.cancel",
    "stt.cancelled",
])
def test_classify_stop_markers(harness, name):
    ev = harness.classify_event({"ts": 1.0, "event": name})
    assert ev is not None and ev["kind"] == "cancel"


def test_classify_drops_unrelated(harness):
    assert harness.classify_event({"event": "orb.qt_stats", "ts": 1.0}) is None
    assert harness.classify_event({"event": "voice_edit.talked"}) is None
    assert harness.classify_event("not-a-dict") is None
    assert harness.classify_event({}) is None
    # A talk row with a non-numeric ts stays, with ts normalized to None.
    ev = harness.classify_event({"event": "voice_edit.talk.error", "ts": "x"})
    assert ev is not None and ev["ts"] is None


# --------------------------------------------------------------------------- #
# build_timeline
# --------------------------------------------------------------------------- #
def test_build_timeline_orders_stop_sequence(harness):
    events = [
        {"ts": 12.0, "kind": "talk", "type": "response.done",
         "turn_id": "turn-7", "seq": 3, "pid": 9},
        {"ts": 10.0, "kind": "talk", "type": "interrupt",
         "turn_id": "turn-7", "seq": 1, "pid": 9},
        {"ts": 11.0, "kind": "talk", "type": "playback.stopped",
         "turn_id": "turn-7", "seq": 2, "pid": 9},
    ]
    text = harness.build_timeline(events, 10.0)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[0].endswith("interrupt") or "interrupt" in lines[0]
    assert "interrupt" in lines[0] and "+0.000" in lines[0]
    assert "playback.stopped" in lines[1] and "+1.000" in lines[1]
    assert "response.done" in lines[2] and "+2.000" in lines[2]
    assert "turn-7" in lines[0]


def test_build_timeline_marks_preroll_and_missing_ts(harness):
    events = [
        {"ts": 5.0, "kind": "talk", "type": "response.started",
         "turn_id": "turn-1", "seq": 1, "pid": 1, "pre": True},
        {"ts": None, "kind": "talk", "type": "error",
         "turn_id": None, "seq": 2, "pid": 1},
    ]
    text = harness.build_timeline(events, 6.0)
    assert "[pre]" in text
    assert "  ?    " in text  # missing ts renders as unknown, not a crash


# --------------------------------------------------------------------------- #
# run() end-to-end (hermetic: fake window + fake frame grabber)
# --------------------------------------------------------------------------- #
def _talk(ts, boundary, turn="turn-1", seq=1):
    return json.dumps({"ts": ts, "event": f"voice_edit.talk.{boundary}",
                       "turn_id": turn, "seq": seq, "pid": 9}) + "\n"


def test_run_writes_frames_and_timeline(harness, tmp_path, monkeypatch):
    journal = tmp_path / "events.jsonl"
    # Pre-window context: the response that the (simulated) stop interrupts.
    journal.write_text(_talk(100.0, "response.started", seq=1), encoding="utf-8")

    state = {"appended": False}

    def fake_grab(wid, path):
        assert wid == "0xabc"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG-fake")
        if not state["appended"]:
            # On the first frame, a stop press lands in the journal.
            state["appended"] = True
            with journal.open("a", encoding="utf-8") as fh:
                fh.write(_talk(101.0, "interrupt", seq=2))
                fh.write(_talk(101.1, "playback.stopped", seq=3))
                fh.write(_talk(101.2, "response.done", seq=4))
        return True

    def fake_find(title):
        return "0xabc", {"x": 10, "y": 20, "width": 320, "height": 320}

    summary = harness.run(
        journal=journal, out_dir=tmp_path / "out", frames=4, interval=0.1,
        find_window_fn=fake_find, grab_frame_fn=fake_grab,
        sleep_fn=lambda _s: None, now_fn=lambda: 1000.0,
    )

    assert summary["live_run"] is True
    assert summary["frames_captured"] == 4
    assert summary["window_id"] == "0xabc"
    assert summary["geometry"]["width"] == 320
    for path_str in summary["frame_paths"]:
        assert Path(path_str).is_file()
    cap = Path(summary["capture_dir"])
    assert (cap / "frame-000.png").is_file()
    assert (cap / "frame-003.png").is_file()
    assert (cap / "timeline.txt").is_file()
    events = json.loads((cap / "events.json").read_text(encoding="utf-8"))

    window_events = [e for e in events if not e.get("pre")]
    types = [e["type"] for e in window_events]
    assert types == ["interrupt", "playback.stopped", "response.done"]
    # Pre-window context is preserved but flagged, not counted in the window.
    assert any(e.get("pre") for e in events)
    assert summary["event_counts"]["talk.interrupt"] == 1
    assert summary["event_counts"]["talk.response.done"] == 1
    assert summary["event_total"] == 3
    text = (cap / "timeline.txt").read_text(encoding="utf-8")
    assert "interrupt" in text and "playback.stopped" in text
    assert "[pre]" in text


def test_run_degrades_without_window(harness, tmp_path):
    journal = tmp_path / "events.jsonl"
    journal.write_text("", encoding="utf-8")
    summary = harness.run(
        journal=journal, out_dir=tmp_path / "out", frames=3, interval=0.0,
        find_window_fn=lambda _t: None, grab_frame_fn=lambda _w, _p: True,
        sleep_fn=lambda _s: None,
    )
    assert summary["live_run"] is False
    assert summary["frames_captured"] == 0
    assert any("no visible window" in note for note in summary["degraded"])
    # The timeline is still written even with no frames.
    assert Path(summary["timeline_path"]).is_file()


def test_run_degrades_without_journal(harness, tmp_path):
    missing = tmp_path / "nope.jsonl"
    summary = harness.run(
        journal=missing, out_dir=tmp_path / "out", frames=2, interval=0.0,
        find_window_fn=lambda _t: None, grab_frame_fn=lambda _w, _p: True,
        sleep_fn=lambda _s: None,
    )
    assert summary["journal_available"] is False
    assert any("journal not found" in note for note in summary["degraded"])
    assert summary["event_total"] == 0
    assert Path(summary["timeline_path"]).is_file()


# --------------------------------------------------------------------------- #
# resolve_journal_path + synthetic-press gate
# --------------------------------------------------------------------------- #
def test_resolve_journal_path_explicit_wins(harness, tmp_path):
    explicit = tmp_path / "custom.jsonl"
    assert harness.resolve_journal_path(str(explicit)) == explicit
    monkeypatch_env = None  # noqa: F841 - clarity: no env involved for explicit


def test_synthetic_press_is_fail_closed(harness, monkeypatch):
    monkeypatch.delenv(harness.SYNTHETIC_INPUT_ENV, raising=False)
    # Without the env gate it must refuse and send nothing, even with a window.
    assert harness.simulate_press("0xabc", {"x": 0, "y": 0,
                                            "width": 10, "height": 10}) is False
