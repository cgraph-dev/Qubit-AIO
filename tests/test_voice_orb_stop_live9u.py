"""Hermetic pins for the live orb STOP harness (``scripts/verify_orb_stop_live.py``).

The live part (a real player subprocess + SIGUSR2 + journal) is best-effort on
a machine with working audio; what a refactor could silently break is the
parsing/verdict logic. These tests pin:

  * ``voice_edit.talk.*`` rows are recognized and everything else is dropped;
  * ``summarize`` requires the canonical ``interrupt`` -> ``playback.stopped``
    -> ``response.done`` order AND a player that ended, and degrades cleanly
    when no player could start;
  * the wav padding keeps a short synth'd clip alive long enough for a stop;
  * ``run`` writes ``evidence.json`` + ``timeline.txt`` and returns DEGRADED
    (no traceback) when no player is installed;
  * the gated ``--speak-test`` path refuses without the opt-in env flag and
    verifies a real service-TTS -> host-player stop without fabricating a
    helper triple it never emitted (and still requires that triple when the
    helper really owned playback).
"""
from __future__ import annotations

import importlib.util
import io
import json
import signal
import sys
import wave
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_orb_stop_live.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "verify_orb_stop_live_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    assert SCRIPT_PATH.is_file(), f"harness missing: {SCRIPT_PATH}"
    return _load_harness()


def _event(etype, ts, seq, turn="turn-1"):
    return {"ts": ts, "event": f"voice_edit.talk.{etype}", "type": etype,
            "turn_id": turn, "seq": seq, "pid": 1}


def test_classify_keeps_talk_rows_only(harness):
    ev = harness.classify_talk_row({
        "ts": 5.5, "event": "voice_edit.talk.interrupt",
        "turn_id": "turn-2", "seq": 9, "pid": 77})
    assert ev == {"ts": 5.5, "event": "voice_edit.talk.interrupt",
                  "type": "interrupt", "turn_id": "turn-2", "seq": 9,
                  "pid": 77}
    assert harness.classify_talk_row(
        {"event": "voice_edit.cancel", "ts": 1.0}) is None
    assert harness.classify_talk_row({"event": "tts.synthesize"}) is None
    assert harness.classify_talk_row("nope") is None
    # Non-numeric ts is normalized, not dropped.
    ev = harness.classify_talk_row({"event": "voice_edit.talk.error",
                                    "ts": "x"})
    assert ev is not None and ev["ts"] is None


def test_summarize_pass_requires_ordered_triple_and_player_exit(harness):
    events = [
        _event("playback.started", 100.0, 1),
        _event("interrupt", 100.5, 2),
        _event("playback.stopped", 100.6, 3),
        _event("response.done", 100.7, 4),
    ]
    out = harness.summarize(events, t_stop=100.4, player_started=True,
                            player_exited=True)
    assert out["verdict"] == "PASS", out
    assert out["triple_present"] is True and out["triple_ordered"] is True
    assert out["stop_latency_s"] == pytest.approx(0.1, abs=0.001)


def test_summarize_missing_or_out_of_order_triple_fails(harness):
    missing = [_event("interrupt", 1.0, 1), _event("response.done", 1.2, 2)]
    out = harness.summarize(missing, player_started=True, player_exited=True)
    assert out["verdict"] == "FAIL" and "playback.stopped" in out["missing"]

    out_of_order = [
        _event("playback.stopped", 1.0, 1),
        _event("interrupt", 1.1, 2),
        _event("response.done", 1.2, 3),
    ]
    out = harness.summarize(out_of_order, player_started=True,
                            player_exited=True)
    assert out["verdict"] == "FAIL" and out["triple_present"] is True


def test_summarize_degrades_without_a_player(harness):
    out = harness.summarize([], player_started=False, player_exited=None,
                            degraded=["no paplay/aplay installed"])
    assert out["verdict"] == "DEGRADED"
    assert "no paplay/aplay" in " ".join(out["degraded"])


def test_summarize_fails_when_player_survives_the_stop(harness):
    events = [
        _event("interrupt", 1.0, 1),
        _event("playback.stopped", 1.1, 2),
        _event("response.done", 1.2, 3),
    ]
    out = harness.summarize(events, player_started=True, player_exited=False)
    assert out["verdict"] == "FAIL"
    assert "still alive" in out["reason"]


def test_build_timeline_orders_by_ts_and_marks_relative(harness):
    events = [_event("response.done", 2.0, 3), _event("interrupt", 1.0, 2)]
    text = harness.build_timeline(events, 0.5, header=["hdr"])
    lines = text.splitlines()
    assert lines[0] == "hdr"
    assert "interrupt" in lines[1] and "response.done" in lines[2]


def test_pad_wav_keeps_a_short_clip_playing(harness):
    wav = harness.make_tone_wav(0.2)
    padded = harness.pad_wav_min_duration(wav, 2.0)
    with wave.open(io.BytesIO(padded), "rb") as wf:
        duration = wf.getnframes() / float(wf.getframerate())
    assert duration >= 1.9
    # Already-long audio is returned untouched.
    assert harness.pad_wav_min_duration(padded, 1.0) == padded


def test_run_degrades_cleanly_without_a_player(harness, tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "_playback_tools", lambda: [])
    out = harness.run(out_dir=tmp_path, duration=0.5)
    assert out["verdict"] == "DEGRADED", out
    cap = Path(out["capture_dir"])
    assert (cap / "evidence.json").is_file()
    assert (cap / "timeline.txt").is_file()
    assert json.loads((cap / "evidence.json").read_text())["verdict"] == "DEGRADED"


# ---------------------------------------------------------------------------
# live-helper mode (read-only): pure parsing + injected /proc/journal
# ---------------------------------------------------------------------------
def _live_event(etype, ts, seq, turn="turn-live"):
    return {"ts": ts, "event": f"voice_edit.talk.{etype}", "type": etype,
            "turn_id": turn, "seq": seq, "pid": 1}


def _write_proc(tmp_path, pid, *, sigcgt, cmdline="python -m voicebridge.desktop_helper"):
    d = Path(tmp_path) / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "status").write_text(f"Name:\tpython\nSigCgt:\t{sigcgt:016x}\n",
                              encoding="utf-8")
    (d / "cmdline").write_bytes(cmdline.replace(" ", "\0").encode() + b"\0")
    return d


def _signal_clock(start, step=0.05):
    """Frozen until the stop signal is sent, then advancing (bounded wait)."""
    state = {"t": float(start), "go": False}

    def now():
        if state["go"]:
            state["t"] += step
        return state["t"]

    def release():
        state["go"] = True

    return now, release


def _write_journal(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n",
                    encoding="utf-8")


def test_handler_installed_reads_sigcgt(harness, tmp_path):
    # SIGUSR1 = bit 9, SIGUSR2 = bit 11.
    assert signal.SIGUSR1 == 10 and signal.SIGUSR2 == 12
    _write_proc(tmp_path, 11, sigcgt=(1 << 9) | (1 << 11))
    mask = harness.read_proc_signal_mask(11, tmp_path)
    assert mask == (1 << 9) | (1 << 11)
    assert harness.handler_installed(mask, signal.SIGUSR1) is True
    assert harness.handler_installed(mask, signal.SIGUSR2) is True

    _write_proc(tmp_path, 12, sigcgt=(1 << 9))
    only_usr1 = harness.read_proc_signal_mask(12, tmp_path)
    assert harness.handler_installed(only_usr1, signal.SIGUSR1) is True
    assert harness.handler_installed(only_usr1, signal.SIGUSR2) is False
    assert harness.handler_installed(None, signal.SIGUSR2) is False
    assert harness.read_proc_signal_mask(999, tmp_path) is None


def test_resolve_live_helper_validates_heartbeat_and_cmdline(harness, tmp_path):
    _write_proc(tmp_path, 4242, sigcgt=(1 << 11))
    hb = {"pid": 4242, "state": "idle"}
    out = harness.resolve_live_helper(proc_root=tmp_path,
                                      live_fn=lambda: True,
                                      read_fn=lambda: hb)
    assert out["ok"] is True and out["pid"] == 4242
    assert harness.HELPER_CMDLINE_MARK in out["cmdline"]

    stale = harness.resolve_live_helper(proc_root=tmp_path,
                                        live_fn=lambda: False,
                                        read_fn=lambda: hb)
    assert stale["ok"] is False and "heartbeat" in stale["reason"]

    _write_proc(tmp_path, 4243, sigcgt=(1 << 11), cmdline="python unrelated")
    foreign = harness.resolve_live_helper(
        proc_root=tmp_path, live_fn=lambda: True,
        read_fn=lambda: {"pid": 4243, "state": "idle"})
    assert foreign["ok"] is False and "cmdline" in foreign["reason"]


def test_read_tail_talk_rows_drops_partial_line(harness, tmp_path):
    path = tmp_path / "events.jsonl"
    first = {"event": "voice_edit.talk.playback.started", "ts": 1.0, "seq": 1,
             "turn_id": "t"}
    noise = {"event": "unrelated", "ts": 1.1}
    last = {"event": "voice_edit.talk.playback.stopped", "ts": 2.0, "seq": 2,
            "turn_id": "t"}
    _write_journal(path, [first, noise, last])
    events, size = harness.read_tail_talk_rows(path, 10 ** 9)
    assert [e["type"] for e in events] == ["playback.started", "playback.stopped"]
    assert size == path.stat().st_size

    # A tail that starts exactly on a line boundary keeps that line.
    last_line = json.dumps(last)
    events, _ = harness.read_tail_talk_rows(path, len(last_line) + 1)
    assert [e["type"] for e in events] == ["playback.stopped"]

    # A tail that starts mid-line drops only the partial first line.
    done = {"event": "voice_edit.talk.response.done", "ts": 3.0, "seq": 3,
            "turn_id": "t"}
    _write_journal(path, [first, noise, last, done])
    done_line = json.dumps(done)
    events, _ = harness.read_tail_talk_rows(path, len(done_line) + 5)
    assert [e["type"] for e in events] == ["response.done"]

    missing, size = harness.read_tail_talk_rows(tmp_path / "nope", 100)
    assert missing == [] and size == 0


def test_analyze_playback_open_stale_and_closed(harness):
    started = [_live_event("playback.started", 100.0, 1)]
    live = harness.analyze_playback(started, now=101.0, stale_after=120.0)
    assert live["speaking"] is True and live["stale"] is False
    assert live["started_age_s"] == pytest.approx(1.0)

    old = harness.analyze_playback(started, now=1000.0, stale_after=120.0)
    assert old["speaking"] is False and old["stale"] is True

    closed = started + [_live_event("playback.stopped", 101.0, 2)]
    out = harness.analyze_playback(closed, now=102.0)
    assert out["speaking"] is False and out["stale"] is False
    assert out["last_boundary"]["type"] == "playback.stopped"

    # response.done closes an error/empty turn even without playback.stopped.
    errored = started + [_live_event("response.done", 101.0, 2)]
    assert harness.analyze_playback(errored, now=102.0)["speaking"] is False


def test_live_check_degrades_without_a_live_helper(harness, tmp_path):
    out = harness.live_check(journal=tmp_path / "events.jsonl",
                             live_fn=lambda: False, read_fn=lambda: None,
                             now_fn=lambda: 100.0)
    assert out["verdict"] == "DEGRADED"
    assert out["signal_sent"] is False
    assert "heartbeat" in out["reason"]


def test_live_check_never_signals_without_allow(harness, tmp_path):
    _write_proc(tmp_path, 555, sigcgt=(1 << 9) | (1 << 11))
    journal = tmp_path / "events.jsonl"
    _write_journal(journal, [_live_event("playback.started", 100.0, 1)])
    calls = []

    out = harness.live_check(
        allow_signal=False, journal=journal, proc_root=tmp_path,
        live_fn=lambda: True,
        read_fn=lambda: {"pid": 555, "state": "idle"},
        now_fn=lambda: 100.5, sleep_fn=lambda _s: None,
        kill_fn=lambda pid, sig: calls.append((pid, sig)),
        alive_fn=lambda _pid: True)

    assert out["playback"]["speaking"] is True
    assert out["signal_sent"] is False and calls == []
    assert out["verdict"] == "DEGRADED"
    assert "read-only" in out["reason"]


def test_live_check_refuses_signal_without_sigusr2_handler(harness, tmp_path):
    # Only SIGUSR1 caught: a SIGUSR2 would terminate the helper.
    _write_proc(tmp_path, 556, sigcgt=(1 << 9))
    journal = tmp_path / "events.jsonl"
    _write_journal(journal, [_live_event("playback.started", 100.0, 1)])
    calls = []

    out = harness.live_check(
        allow_signal=True, journal=journal, proc_root=tmp_path,
        live_fn=lambda: True,
        read_fn=lambda: {"pid": 556, "state": "idle"},
        now_fn=lambda: 100.5, sleep_fn=lambda _s: None,
        kill_fn=lambda pid, sig: calls.append((pid, sig)),
        alive_fn=lambda _pid: True)

    assert out["sigusr2_handler"] is False
    assert calls == [] and out["signal_sent"] is False
    assert out["verdict"] == "DEGRADED"
    assert "refusing to signal" in out["reason"]


def test_live_check_idle_survival_probe_is_degraded_not_pass(harness, tmp_path):
    _write_proc(tmp_path, 557, sigcgt=(1 << 9) | (1 << 11))
    journal = tmp_path / "events.jsonl"
    _write_journal(journal, [
        _live_event("playback.started", 100.0, 1),
        _live_event("playback.stopped", 100.2, 2),
    ])
    calls = []

    out = harness.live_check(
        allow_signal=True, journal=journal, proc_root=tmp_path,
        live_fn=lambda: True,
        read_fn=lambda: {"pid": 557, "state": "idle"},
        now_fn=lambda: 101.0, sleep_fn=lambda _s: None,
        kill_fn=lambda pid, sig: calls.append((pid, sig)),
        alive_fn=lambda _pid: True)

    assert calls == [(557, signal.SIGUSR2)]
    assert out["signal_sent"] is True
    assert out["survives_sigusr2"] is True
    assert out["verdict"] == "DEGRADED"
    assert "idle" in out["reason"]


def test_live_check_refuses_idle_probe_while_busy(harness, tmp_path):
    _write_proc(tmp_path, 558, sigcgt=(1 << 9) | (1 << 11))
    journal = tmp_path / "events.jsonl"
    _write_journal(journal, [_live_event("response.done", 100.0, 1)])
    calls = []

    out = harness.live_check(
        allow_signal=True, journal=journal, proc_root=tmp_path,
        live_fn=lambda: True,
        read_fn=lambda: {"pid": 558, "state": "processing"},
        now_fn=lambda: 101.0, sleep_fn=lambda _s: None,
        kill_fn=lambda pid, sig: calls.append((pid, sig)),
        alive_fn=lambda _pid: True)

    assert calls == [] and out["signal_sent"] is False
    assert out["verdict"] == "DEGRADED"
    assert "refusing idle stop probe" in out["reason"]


def test_live_check_speaking_stop_passes_with_ordered_triple(harness, tmp_path):
    _write_proc(tmp_path, 559, sigcgt=(1 << 9) | (1 << 11))
    journal = tmp_path / "events.jsonl"
    _write_journal(journal, [_live_event("playback.started", 199.0, 1)])

    now_fn, release = _signal_clock(200.0)

    def fake_kill(pid, sig):
        assert (pid, sig) == (559, signal.SIGUSR2)
        release()
        with journal.open("a", encoding="utf-8") as fh:
            for seq, etype in enumerate(
                    ("interrupt", "playback.stopped", "response.done"), start=2):
                fh.write(json.dumps(_live_event(etype, 200.0 + seq / 100, seq))
                         + "\n")

    out = harness.live_check(
        allow_signal=True, journal=journal, proc_root=tmp_path,
        live_fn=lambda: True,
        read_fn=lambda: {"pid": 559, "state": "idle"},
        now_fn=now_fn, sleep_fn=lambda _s: None,
        kill_fn=fake_kill, alive_fn=lambda _pid: True)

    assert out["signal_sent"] is True
    assert out["helper_alive_after"] is True
    assert out["triple_present"] is True and out["triple_ordered"] is True
    assert out["stop_latency_s"] == pytest.approx(0.02)
    assert out["verdict"] == "PASS", out


def test_live_check_fails_when_helper_does_not_survive(harness, tmp_path):
    _write_proc(tmp_path, 560, sigcgt=(1 << 9) | (1 << 11))
    journal = tmp_path / "events.jsonl"
    _write_journal(journal, [_live_event("playback.started", 199.0, 1)])
    now_fn, release = _signal_clock(200.0)

    def fake_kill(_pid, _sig):
        release()

    out = harness.live_check(
        allow_signal=True, journal=journal, proc_root=tmp_path,
        live_fn=lambda: True,
        read_fn=lambda: {"pid": 560, "state": "idle"},
        now_fn=now_fn, sleep_fn=lambda _s: None,
        kill_fn=fake_kill,
        alive_fn=lambda _pid: False)

    assert out["signal_sent"] is True
    assert out["verdict"] == "FAIL"
    assert "did not survive" in out["reason"]


def test_run_live_writes_evidence(harness, tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "live_check", lambda **_kw: {
        "mode": "live", "verdict": "DEGRADED",
        "reason": "live helper is not speaking; read-only",
        "degraded": ["no open playback"], "events": [],
        "events_after_stop": []})
    out = harness.run_live(out_dir=tmp_path)
    assert out["verdict"] == "DEGRADED"
    cap = Path(out["capture_dir"])
    assert (cap / "evidence.json").is_file()
    assert (cap / "timeline.txt").is_file()
    assert json.loads((cap / "evidence.json").read_text())["verdict"] == "DEGRADED"


def test_live_check_ignores_triple_from_another_turn(harness, tmp_path):
    _write_proc(tmp_path, 561, sigcgt=(1 << 9) | (1 << 11))
    journal = tmp_path / "events.jsonl"
    _write_journal(journal, [_live_event("playback.started", 199.0, 1)])
    now_fn, release = _signal_clock(200.0)

    def fake_kill(_pid, _sig):
        release()
        with journal.open("a", encoding="utf-8") as fh:
            for seq, etype in enumerate(
                    ("interrupt", "playback.stopped", "response.done"), start=2):
                fh.write(json.dumps(_live_event(
                    etype, 200.0 + seq / 100, seq, turn="other-turn")) + "\n")

    out = harness.live_check(
        allow_signal=True, journal=journal, proc_root=tmp_path,
        live_fn=lambda: True,
        read_fn=lambda: {"pid": 561, "state": "idle"},
        now_fn=now_fn, sleep_fn=lambda _s: None,
        kill_fn=fake_kill, alive_fn=lambda _pid: True)

    assert out["signal_sent"] is True
    assert out["triple_ordered"] is False
    assert out["verdict"] == "FAIL"
    assert "missing" in out["reason"]


# ---------------------------------------------------------------------------
# gated speak-test: pure decision logic + injected service/player seams
# ---------------------------------------------------------------------------
def _service_stop(ts, had_playback, pid=1):
    return {"ts": ts, "event": "interrupt.request",
            "type": "interrupt.request", "had_playback": had_playback,
            "announce": False, "pid": pid}


def test_speak_test_gate_requires_the_env_flag(harness):
    assert harness.speak_test_allowed({}) is False
    assert harness.speak_test_allowed({harness.SPEAK_TEST_ENV: "0"}) is False
    for value in ("1", "true", "YES", "on"):
        assert harness.speak_test_allowed({harness.SPEAK_TEST_ENV: value}) is True


def test_classify_and_analyze_service_stop(harness):
    row = harness.classify_service_row(
        {"ts": 5.0, "event": "interrupt.request", "had_playback": True,
         "announce": False, "pid": 9})
    assert row["type"] == "interrupt.request" and row["had_playback"] is True
    assert harness.classify_service_row({"event": "tts.speak"}) is None
    assert harness.classify_service_row("nope") is None

    out = harness.analyze_service_stop(
        [_service_stop(4.0, True), _service_stop(6.0, False)], t_stop=5.0)
    assert out["stop_seen"] is True and out["count"] == 1
    assert out["had_playback"] is False  # newest relevant row wins

    none = harness.analyze_service_stop([_service_stop(4.0, True)],
                                        t_stop=5.0)
    assert none["stop_seen"] is False and none["had_playback"] is None


def test_read_service_rows_is_append_safe(harness, tmp_path):
    path = tmp_path / "events.jsonl"
    _write_journal(path, [_service_stop(1.0, True)])
    rows, end = harness.read_service_rows(path)
    assert [r["type"] for r in rows] == ["interrupt.request"]
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_service_stop(2.0, False)) + "\n")
    rows2, _ = harness.read_service_rows(path, end)
    assert len(rows2) == 1 and rows2[0]["had_playback"] is False


def test_summarize_speak_test_real_service_player_passes_without_triple(harness):
    out = harness.summarize_speak_test(
        provider="piper", wav_bytes=4096, player_started=True,
        player_exited=True, playback_live_before_stop=True,
        helper_pid=7, helper_alive_after=True,
        triple={"applicable": False, "present": False, "ordered": False},
        service_stop={"stop_seen": True, "had_playback": False})
    assert out["verdict"] == "PASS", out
    assert out["triple_applicable"] is False
    assert "no helper triple applies" in out["reason"]


def test_summarize_speak_test_degrades_without_provider_or_player(harness):
    no_tts = harness.summarize_speak_test(
        provider="", wav_bytes=0, player_started=False,
        playback_live_before_stop=None)
    assert no_tts["verdict"] == "DEGRADED"
    assert "no TTS audio" in no_tts["reason"]

    no_player = harness.summarize_speak_test(
        provider="piper", wav_bytes=10, player_started=False,
        playback_live_before_stop=None)
    assert no_player["verdict"] == "DEGRADED"
    assert "player never started" in no_player["reason"]

    died = harness.summarize_speak_test(
        provider="piper", wav_bytes=10, player_started=True,
        playback_live_before_stop=False)
    assert died["verdict"] == "DEGRADED"
    assert "before the stop" in died["reason"]


def test_summarize_speak_test_fails_linger_dead_helper_and_missing_triple(harness):
    linger = harness.summarize_speak_test(
        provider="piper", wav_bytes=10, player_started=True,
        player_exited=False, playback_live_before_stop=True,
        helper_pid=7, helper_alive_after=True)
    assert linger["verdict"] == "FAIL" and "still alive" in linger["reason"]

    dead = harness.summarize_speak_test(
        provider="piper", wav_bytes=10, player_started=True,
        player_exited=True, playback_live_before_stop=True,
        helper_pid=7, helper_alive_after=False)
    assert dead["verdict"] == "FAIL" and "survive" in dead["reason"]

    missing = harness.summarize_speak_test(
        provider="piper", wav_bytes=10, player_started=True,
        player_exited=True, playback_live_before_stop=True,
        helper_pid=7, helper_alive_after=True,
        triple={"applicable": True, "present": False,
                "missing": ["playback.stopped"]})
    assert missing["verdict"] == "FAIL"
    assert "playback.stopped" in missing["reason"]


class _FakeProc:
    def __init__(self, *, alive_after_stop=False, linger=False,
                 kill_fails=False):
        self.pid = 4242
        self.args = ["paplay", "x.wav"]
        self._alive = True
        self.terminated = False
        self.killed = False
        self._alive_after_stop = alive_after_stop
        self._linger = linger
        self._kill_fails = kill_fails

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        if not self._linger:
            self._alive = bool(self._alive_after_stop)

    def kill(self):
        self.killed = True
        if self._kill_fails:
            raise RuntimeError("cannot kill")
        self._alive = False

    def wait(self, timeout=None):
        if self._linger:
            raise TimeoutError("still alive")
        self._alive = bool(self._alive_after_stop)
        return 0


def _fake_helper():
    return {"ok": True, "pid": 777, "heartbeat": {"pid": 777, "state": "idle"},
            "cmdline": "python -m voicebridge.desktop_helper", "reason": ""}


def test_run_speak_test_is_blocked_without_the_gate(harness, tmp_path, monkeypatch):
    monkeypatch.delenv(harness.SPEAK_TEST_ENV, raising=False)
    out = harness.run_speak_test(out_dir=tmp_path, text="hi")
    assert out["verdict"] == "BLOCKED"
    assert harness.SPEAK_TEST_ENV in out["reason"]
    assert (Path(out["capture_dir"]) / "evidence.json").is_file()


def test_run_speak_test_degrades_without_a_helper(harness, tmp_path, monkeypatch):
    monkeypatch.setenv(harness.SPEAK_TEST_ENV, "1")
    out = harness.run_speak_test(
        out_dir=tmp_path,
        resolve_fn=lambda **_kw: {"ok": False, "reason": "helper not live",
                                  "pid": None, "heartbeat": None,
                                  "cmdline": ""})
    assert out["verdict"] == "DEGRADED"
    assert "helper not live" in out["reason"]


def test_run_speak_test_refuses_without_sigusr2_handler(harness, tmp_path,
                                                        monkeypatch):
    monkeypatch.setenv(harness.SPEAK_TEST_ENV, "1")
    monkeypatch.setattr(harness, "read_proc_signal_mask", lambda _pid: 1 << 9)
    out = harness.run_speak_test(out_dir=tmp_path,
                                 resolve_fn=lambda **_kw: _fake_helper())
    assert out["verdict"] == "DEGRADED"
    assert "SIGUSR2 handler" in out["reason"]


def test_run_speak_test_degrades_when_service_tts_is_unavailable(
        harness, tmp_path, monkeypatch):
    monkeypatch.setenv(harness.SPEAK_TEST_ENV, "1")
    monkeypatch.setattr(harness, "read_proc_signal_mask", lambda _pid: 1 << 11)
    out = harness.run_speak_test(
        out_dir=tmp_path, journal=tmp_path / "events.jsonl",
        resolve_fn=lambda **_kw: _fake_helper(),
        serve_fn=lambda: ("http://127.0.0.1:9", ""),
        speak_fn=lambda _t: {"ok": False, "wav": b"", "provider": "",
                             "code": "AUDIO_TTS_UNAVAILABLE",
                             "detail": "no voice pack"})
    assert out["verdict"] == "DEGRADED"
    assert "AUDIO_TTS_UNAVAILABLE" in out["reason"]


def test_run_speak_test_real_player_path_passes(harness, tmp_path, monkeypatch):
    monkeypatch.setenv(harness.SPEAK_TEST_ENV, "1")
    monkeypatch.setattr(harness, "read_proc_signal_mask", lambda _pid: 1 << 11)
    journal = tmp_path / "events.jsonl"
    journal.write_text("", encoding="utf-8")
    signals = []

    def interrupt(mode="stop"):
        with journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_service_stop(1000.0, False)) + "\n")
        return {"ok": True}

    out = harness.run_speak_test(
        out_dir=tmp_path, journal=journal, text="Qubit live stop test.",
        resolve_fn=lambda **_kw: _fake_helper(),
        serve_fn=lambda: ("http://127.0.0.1:9", ""),
        speak_fn=lambda _t: {"ok": True, "wav": b"RIFF0000", "provider": "piper"},
        interrupt_fn=interrupt,
        player_fn=lambda _w, _d: _FakeProc(),
        signal_fn=lambda pid, sig: signals.append((pid, sig)),
        alive_fn=lambda _pid: True,
        now_fn=lambda: 1000.0, sleep_fn=lambda _s: None)
    assert out["verdict"] == "PASS", out
    assert out["provider"] == "piper" and out["wav_bytes"] == 8
    assert out["playback_live_before_stop"] is True
    assert out["player_exited"] is True and out["helper_alive_after"] is True
    assert signals == [(777, signal.SIGUSR2)]
    assert out["triple_applicable"] is False
    assert out["service_stop"]["stop_seen"] is True
    cap = Path(out["capture_dir"])
    assert (cap / "evidence.json").is_file()
    assert json.loads((cap / "evidence.json").read_text())["verdict"] == "PASS"


def test_run_speak_test_fails_when_the_player_lingers(harness, tmp_path,
                                                      monkeypatch):
    monkeypatch.setenv(harness.SPEAK_TEST_ENV, "1")
    monkeypatch.setattr(harness, "read_proc_signal_mask", lambda _pid: 1 << 11)
    journal = tmp_path / "events.jsonl"
    journal.write_text("", encoding="utf-8")
    proc = _FakeProc(linger=True, kill_fails=True)
    clock = {"t": 1000.0}

    def now():
        clock["t"] += 0.05
        return clock["t"]

    out = harness.run_speak_test(
        out_dir=tmp_path, journal=journal,
        resolve_fn=lambda **_kw: _fake_helper(),
        serve_fn=lambda: ("http://127.0.0.1:9", ""),
        speak_fn=lambda _t: {"ok": True, "wav": b"RIFF0000", "provider": "piper"},
        interrupt_fn=lambda mode="stop": {"ok": True},
        player_fn=lambda _w, _d: proc,
        signal_fn=lambda _pid, _sig: None,
        alive_fn=lambda _pid: True,
        now_fn=now, sleep_fn=lambda _s: None)
    assert out["verdict"] == "FAIL"
    assert proc.terminated is True and proc.killed is True
    assert "still alive" in out["reason"]


def test_run_speak_test_requires_triple_when_the_helper_owned_playback(
        harness, tmp_path, monkeypatch):
    monkeypatch.setenv(harness.SPEAK_TEST_ENV, "1")
    monkeypatch.setattr(harness, "read_proc_signal_mask", lambda _pid: 1 << 11)
    journal = tmp_path / "events.jsonl"
    _write_journal(journal, [_live_event("playback.started", 999.0, 1)])

    def kill(pid, sig):
        assert (pid, sig) == (777, signal.SIGUSR2)
        with journal.open("a", encoding="utf-8") as fh:
            for seq, etype in enumerate(
                    ("interrupt", "playback.stopped", "response.done"), start=2):
                fh.write(json.dumps(_live_event(
                    etype, 1000.0 + seq / 100, seq)) + "\n")

    out = harness.run_speak_test(
        out_dir=tmp_path, journal=journal,
        resolve_fn=lambda **_kw: _fake_helper(),
        serve_fn=lambda: ("http://127.0.0.1:9", ""),
        speak_fn=lambda _t: {"ok": True, "wav": b"RIFF0000", "provider": "piper"},
        interrupt_fn=lambda mode="stop": {"ok": True},
        player_fn=lambda _w, _d: _FakeProc(),
        signal_fn=kill,
        alive_fn=lambda _pid: True,
        now_fn=lambda: 1000.0, sleep_fn=lambda _s: None)
    assert out["verdict"] == "PASS", out
    assert out["triple_applicable"] is True
    assert out["triple_present"] is True and out["triple_ordered"] is True
