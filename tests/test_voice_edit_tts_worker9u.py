"""A STOP genuinely interrupts a piper/sherpa synth (killable worker, 9u).

``piper``/``sherpa-onnx`` synthesis is a blocking C call, so a Python thread
cannot abort it. ``POST /api/audio/speak`` (``h_audio_speak``) now runs a
KEYED orb reply's synth in a child process owned by the service. A stop that
lands during the call terminates that child (SIGTERM -> SIGKILL) and the
request returns the typed ``AUDIO_SPEAK_CANCELLED`` instead of waiting for a
wav that would be discarded anyway.

These tests are hermetic: the worker's spawn command is a small script that
speaks the same line protocol, so no model, audio, mic or network is used.
KEYED and UNKEYED speak requests both use that worker, so the service process
never holds a second TTS model copy; only the explicit
``QUBIT_TTS_SYNTH_WORKER=0`` opt-out (or a config double without ``data``)
uses the historical in-process provider.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge import qubit_compat as QC  # noqa: E402
from voicebridge.tts import synth_worker as SW  # noqa: E402
from voicebridge.voice_edit import routes as R  # noqa: E402
from voicebridge.voice_edit.service import VoiceEditService  # noqa: E402


class _DataCfg:
    """Config double the worker path accepts (has a ``data`` dict)."""

    def __init__(self, data=None, model_dir="/models"):
        self.data = data if data is not None else {"tts": {"provider": "piper"}}
        self._model_dir = model_dir
        self.path = None

    def get(self, *path, default=None):
        node = self.data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node

    def model_dir(self):
        return self._model_dir


class _SvcCfg(_DataCfg):
    def get(self, *path, default=None):
        if path[:1] == ("voice_edit",):
            base = {"enabled": True, "routing": "local",
                    "confirm_before_replace": False, "speak_answers": True}
            if len(path) > 1:
                return base.get(path[-1], default)
            return base
        return super().get(*path, default=default)


@pytest.fixture(autouse=True)
def _clean_worker():
    SW.shutdown()
    yield
    SW.shutdown()


FAKE_CHILD = r"""
import json, os, sys, time
first = sys.stdin.readline()
try:
    cfg = json.loads(first)
    assert isinstance(cfg.get("config"), dict)
except Exception as exc:
    print(json.dumps({"ready": False, "error": str(exc)}), flush=True)
    raise SystemExit(1)
pidlog = os.environ.get("FAKE_PIDLOG")
if pidlog:
    with open(pidlog, "a") as fh:
        fh.write(str(os.getpid()) + "\n")
ready_s = float(os.environ.get("FAKE_READY_S", "0") or 0)
if ready_s:
    time.sleep(ready_s)
if os.environ.get("FAKE_SLOW_READY"):
    time.sleep(30)
print(json.dumps({"ready": True, "provider": "fake"}), flush=True)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    mode_file = os.environ.get("FAKE_MODE_FILE")
    if mode_file:
        try:
            mode = open(mode_file).read().strip() or "ok"
        except Exception:
            mode = "ok"
    else:
        mode = os.environ.get("FAKE_MODE", "ok")
    if mode == "sleep":
        time.sleep(30)
        continue
    if mode == "error":
        print(json.dumps({"id": req["id"], "ok": False,
                          "error": "boom"}), flush=True)
        continue
    with open(req["out"], "wb") as fh:
        fh.write(b"RIFFfake")
    print(json.dumps({"id": req["id"], "ok": True}), flush=True)
"""


@pytest.fixture()
def fake_child(tmp_path, monkeypatch):
    script = tmp_path / "fake_tts_child.py"
    script.write_text(FAKE_CHILD)
    monkeypatch.setattr(
        SW, "_spawn_cmd", lambda: [sys.executable, str(script)])
    return script


# ---------------------------------------------------------------------------
# feature gate + in-process compatibility
# ---------------------------------------------------------------------------
def test_worker_supported_only_for_data_configs(monkeypatch):
    assert SW.worker_supported(_DataCfg()) is True
    assert SW.worker_supported(object()) is False
    monkeypatch.setenv("QUBIT_TTS_SYNTH_WORKER", "0")
    assert SW.worker_supported(_DataCfg()) is False


def _svc(monkeypatch):
    svc = VoiceEditService(_SvcCfg())
    monkeypatch.setattr(R, "service_for", lambda cfg: svc)
    return svc


def test_keyed_speak_uses_the_killable_worker(monkeypatch, tmp_path):
    calls = []

    def _fake_synth(cfg, text, out, *, stopped=None, timeout=60.0):
        calls.append((text, Path(out)))
        Path(out).write_bytes(b"RIFFworker")
        return "worker-provider"

    monkeypatch.setattr(SW, "synthesize_cancellable", _fake_synth)
    monkeypatch.setattr(QC, "_tts_from_cfg",
                        lambda cfg: pytest.fail("in-process factory used"))

    svc = _svc(monkeypatch)
    status, out = QC.h_audio_speak(
        _SvcCfg(), {}, {"text": "speak this", "request_id": "rid-w"}, {})
    assert status == 200 and out["ok"] is True, out
    assert calls and calls[0][0] == "speak this"
    assert out["provider"] == "worker-provider"


def test_worker_cancel_maps_to_typed_stopped(monkeypatch):
    def _cancelled(cfg, text, out, *, stopped=None, timeout=60.0):
        raise SW.SynthCancelled("stopped")

    monkeypatch.setattr(SW, "synthesize_cancellable", _cancelled)
    status, out = QC.h_audio_speak(
        _SvcCfg(), {}, {"text": "hi", "request_id": "rid-c"}, {})
    assert status == 400 and out["code"] == "AUDIO_SPEAK_CANCELLED", out
    assert "data_url" not in out


def test_worker_unavailable_is_typed_tts_unavailable(monkeypatch):
    def _broken(cfg, text, out, *, stopped=None, timeout=60.0):
        raise SW.SynthWorkerUnavailable("no voice pack")

    monkeypatch.setattr(SW, "synthesize_cancellable", _broken)
    status, out = QC.h_audio_speak(
        _SvcCfg(), {}, {"text": "hi", "request_id": "rid-u"}, {})
    assert status == 403 and out["code"] == "AUDIO_TTS_UNAVAILABLE", out


def test_unkeyed_dashboard_call_uses_the_same_worker(monkeypatch):
    """Unkeyed dashboard speech must NOT build a parent provider copy."""
    calls = []

    def _fake_synth(cfg, text, out, *, stopped=None, timeout=60.0):
        calls.append((text, stopped))
        Path(out).write_bytes(b"RIFFworker")
        return "worker-provider"

    monkeypatch.setattr(SW, "synthesize_cancellable", _fake_synth)
    monkeypatch.setattr(QC, "_tts_from_cfg",
                        lambda cfg: pytest.fail("in-process factory used"))
    status, out = QC.h_audio_speak(_DataCfg(), {}, {"text": "dashboard"}, {})
    assert status == 200 and out["provider"] == "worker-provider"
    assert calls and calls[0][0] == "dashboard"
    assert calls[0][1] is None, "an unkeyed call has no cancel predicate"


def test_worker_opt_out_keeps_the_in_process_path(monkeypatch):
    used = []

    class _T:
        name = "inproc"

        def load(self):
            pass

        def synthesize(self, text, out):
            used.append(text)
            Path(out).write_bytes(b"RIFFinproc")

    monkeypatch.setenv("QUBIT_TTS_SYNTH_WORKER", "0")
    monkeypatch.setattr(QC, "_tts_from_cfg", lambda cfg: _T())
    monkeypatch.setattr(SW, "synthesize_cancellable",
                        lambda *a, **k: pytest.fail("worker used"))
    status, out = QC.h_audio_speak(_DataCfg(), {}, {"text": "dashboard"}, {})
    assert status == 200 and out["provider"] == "inproc"
    assert used == ["dashboard"]


def test_keyed_and_unkeyed_speak_share_one_child(tmp_path, fake_child,
                                                 monkeypatch):
    """The orb turn and a dashboard call reuse ONE worker (one model copy)."""
    import base64

    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    cfg = _DataCfg()
    _svc(monkeypatch)
    for body in ({"text": "dashboard"}, {"text": "orb", "request_id": "rid-s"}):
        status, out = QC.h_audio_speak(cfg, {}, body, {})
        assert status == 200 and out["ok"] is True, out
        assert base64.b64decode(out["data_url"].split(",", 1)[1]) == b"RIFFfake"
        assert out["provider"] == "fake"
    pids = pidlog.read_text().split()
    assert len(pids) == 1, f"expected one warm child, got {pids}"


# ---------------------------------------------------------------------------
# real child: protocol, cancellation, reuse
# ---------------------------------------------------------------------------
def test_worker_synthesizes_and_reuses_one_child(tmp_path, fake_child,
                                                 monkeypatch):
    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    cfg = _DataCfg()
    out = tmp_path / "a.wav"
    provider = SW.synthesize_cancellable(cfg, "hello", out,
                                         stopped=lambda: False, timeout=10)
    assert provider == "fake"
    assert out.read_bytes() == b"RIFFfake"
    out2 = tmp_path / "b.wav"
    SW.synthesize_cancellable(cfg, "again", out2,
                              stopped=lambda: False, timeout=10)
    assert out2.read_bytes() == b"RIFFfake"
    pids = pidlog.read_text().split()
    assert len(pids) == 1, f"expected one warm child, got {pids}"


def test_worker_is_killed_mid_synth_on_stop(tmp_path, fake_child,
                                            monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    cfg = _DataCfg()
    stop = threading.Event()
    timer = threading.Timer(0.35, stop.set)
    timer.start()
    t0 = time.monotonic()
    try:
        with pytest.raises(SW.SynthCancelled):
            SW.synthesize_cancellable(cfg, "long", tmp_path / "x.wav",
                                      stopped=stop.is_set, timeout=20)
    finally:
        timer.cancel()
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"stop did not interrupt the synth ({elapsed:.1f}s)"
    assert SW._WORKER is None
    assert not (tmp_path / "x.wav").exists()


def test_worker_error_is_typed_unavailable(tmp_path, fake_child, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "error")
    with pytest.raises(SW.SynthWorkerUnavailable) as exc:
        SW.synthesize_cancellable(_DataCfg(), "x", tmp_path / "e.wav",
                                  stopped=lambda: False, timeout=10)
    assert "boom" in str(exc.value)


def test_worker_timeout_kills_the_child(tmp_path, fake_child, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    with pytest.raises(SW.SynthWorkerUnavailable):
        SW.synthesize_cancellable(_DataCfg(), "x", tmp_path / "t.wav",
                                  stopped=lambda: False, timeout=0.2)
    assert SW._WORKER is None


def test_bad_config_output_is_typed(tmp_path, fake_child):
    class _Bad(_DataCfg):
        @property
        def data(self):
            return 7  # not a dict -> child refuses

        @data.setter
        def data(self, value):
            pass

    with pytest.raises(SW.SynthWorkerUnavailable):
        SW.synthesize_cancellable(_Bad(), "x", tmp_path / "b.wav",
                                  stopped=lambda: False, timeout=5)


def test_stop_during_model_load_kills_the_child(tmp_path, fake_child,
                                                monkeypatch):
    monkeypatch.setenv("FAKE_SLOW_READY", "1")
    stop = threading.Event()
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    try:
        with pytest.raises(SW.SynthCancelled):
            SW.synthesize_cancellable(_DataCfg(), "x", tmp_path / "l.wav",
                                      stopped=stop.is_set, timeout=20)
    finally:
        timer.cancel()
    assert SW._WORKER is None, "a cancelled load must not leave a child"


# ---------------------------------------------------------------------------
# stop recovery: eager replacement pre-warm + idle reaping
# ---------------------------------------------------------------------------
def _wait_until(pred, timeout=8.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def _stop_during_synth(cfg, out, mode):
    """Cancel a sleeping synth; return elapsed seconds."""
    mode.write_text("sleep")
    stop = threading.Event()
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    t0 = time.monotonic()
    try:
        with pytest.raises(SW.SynthCancelled):
            SW.synthesize_cancellable(cfg, "long", out,
                                      stopped=stop.is_set, timeout=20)
    finally:
        timer.cancel()
    return time.monotonic() - t0


def test_stop_prewarms_a_replacement_worker(tmp_path, fake_child, monkeypatch):
    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    mode = tmp_path / "mode.txt"
    mode.write_text("ok")
    monkeypatch.setenv("FAKE_MODE_FILE", str(mode))
    cfg = _DataCfg()
    SW.synthesize_cancellable(cfg, "one", tmp_path / "a.wav",
                              stopped=lambda: False, timeout=10)
    first_pid = SW._WORKER.proc.pid

    _stop_during_synth(cfg, tmp_path / "stop.wav", mode)

    assert _wait_until(lambda: SW._WORKER is not None
                       and SW._WORKER.alive()), "no replacement was pre-warmed"
    replacement = SW._WORKER
    assert replacement.proc.pid != first_pid, "the stopped worker was reused"
    assert not (tmp_path / "stop.wav").exists()

    mode.write_text("ok")
    t0 = time.monotonic()
    provider = SW.synthesize_cancellable(cfg, "next", tmp_path / "b.wav",
                                         stopped=lambda: False, timeout=10)
    elapsed = time.monotonic() - t0
    assert provider == "fake"
    assert (tmp_path / "b.wav").read_bytes() == b"RIFFfake"
    assert elapsed < 2.0, f"next reply was not warm ({elapsed:.2f}s)"
    assert len(pidlog.read_text().split()) == 2, pidlog.read_text()


def test_prewarm_makes_the_next_reply_warm_despite_a_slow_load(
        tmp_path, fake_child, monkeypatch):
    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    mode = tmp_path / "mode.txt"
    mode.write_text("ok")
    monkeypatch.setenv("FAKE_MODE_FILE", str(mode))
    cfg = _DataCfg()
    SW.synthesize_cancellable(cfg, "one", tmp_path / "a.wav",
                              stopped=lambda: False, timeout=10)
    # Only the replacement load is slow; the first spawn stayed fast.
    monkeypatch.setenv("FAKE_READY_S", "0.8")

    _stop_during_synth(cfg, tmp_path / "stop.wav", mode)

    assert _wait_until(lambda: SW._WORKER is not None
                       and SW._WORKER.alive(), timeout=10), \
        "pre-warm did not finish"
    mode.write_text("ok")
    t0 = time.monotonic()
    SW.synthesize_cancellable(cfg, "next", tmp_path / "b.wav",
                              stopped=lambda: False, timeout=10)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.4, (
        f"next reply paid the load again ({elapsed:.2f}s)")
    assert len(pidlog.read_text().split()) == 2, pidlog.read_text()


def test_next_reply_waits_for_an_inflight_prewarm_without_double_load(
        tmp_path, fake_child, monkeypatch):
    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    mode = tmp_path / "mode.txt"
    mode.write_text("ok")
    monkeypatch.setenv("FAKE_MODE_FILE", str(mode))
    cfg = _DataCfg()
    SW.synthesize_cancellable(cfg, "one", tmp_path / "a.wav",
                              stopped=lambda: False, timeout=10)
    monkeypatch.setenv("FAKE_READY_S", "0.8")

    _stop_during_synth(cfg, tmp_path / "stop.wav", mode)

    # Do not wait for the pre-warm: a reply arriving mid-load must join it,
    # not spawn a third provider.
    mode.write_text("ok")
    SW.synthesize_cancellable(cfg, "next", tmp_path / "b.wav",
                              stopped=lambda: False, timeout=10)
    assert len(pidlog.read_text().split()) == 2, pidlog.read_text()


def test_idle_reaper_releases_the_worker(tmp_path, fake_child, monkeypatch):
    monkeypatch.setenv("QUBIT_TTS_SYNTH_WORKER_IDLE_S", "0.15")
    monkeypatch.setattr(SW, "_REAP_TICK_S", 0.02)
    cfg = _DataCfg()
    SW.synthesize_cancellable(cfg, "hello", tmp_path / "a.wav",
                              stopped=lambda: False, timeout=10)
    pid = SW._WORKER.proc.pid

    assert _wait_until(lambda: SW._WORKER is None, timeout=5), \
        "idle worker was never reaped"
    assert _wait_until(lambda: not _pid_alive(pid), timeout=5), \
        "reaped worker process is still alive"


def test_reaper_does_not_kill_a_busy_worker(tmp_path, fake_child, monkeypatch):
    monkeypatch.setenv("QUBIT_TTS_SYNTH_WORKER_IDLE_S", "0.15")
    monkeypatch.setattr(SW, "_REAP_TICK_S", 0.02)
    mode = tmp_path / "mode.txt"
    mode.write_text("sleep")
    monkeypatch.setenv("FAKE_MODE_FILE", str(mode))
    cfg = _DataCfg()
    stop = threading.Event()
    errors = []

    def _run():
        try:
            SW.synthesize_cancellable(cfg, "long", tmp_path / "a.wav",
                                      stopped=stop.is_set, timeout=20)
        except SW.SynthCancelled:
            pass
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=_run)
    thread.start()
    try:
        assert _wait_until(lambda: SW._WORKER is not None, timeout=5)
        time.sleep(0.5)
        assert SW._WORKER is not None and SW._WORKER.alive(), \
            "the reaper killed a synthesizing worker"
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not errors, errors


def test_shutdown_during_prewarm_does_not_reinstall_a_worker(
        tmp_path, fake_child, monkeypatch):
    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    mode = tmp_path / "mode.txt"
    mode.write_text("ok")
    monkeypatch.setenv("FAKE_MODE_FILE", str(mode))
    cfg = _DataCfg()
    SW.synthesize_cancellable(cfg, "one", tmp_path / "a.wav",
                              stopped=lambda: False, timeout=10)
    monkeypatch.setenv("FAKE_READY_S", "0.3")

    _stop_during_synth(cfg, tmp_path / "stop.wav", mode)
    SW.shutdown()

    time.sleep(0.5)
    assert SW._WORKER is None, "a pre-warm installed after shutdown"

    mode.write_text("ok")
    monkeypatch.setenv("FAKE_READY_S", "0")
    assert SW.synthesize_cancellable(cfg, "next", tmp_path / "b.wav",
                                     stopped=lambda: False, timeout=10) == "fake"
    assert SW._WORKER is not None and SW._WORKER.alive()


# ---------------------------------------------------------------------------
# warm_async: hide the post-idle cold load behind the brain turn
# ---------------------------------------------------------------------------
def test_warm_async_starts_a_cold_worker_in_the_background(
        tmp_path, fake_child, monkeypatch):
    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    monkeypatch.setenv("FAKE_READY_S", "0.8")
    cfg = _DataCfg()

    t0 = time.monotonic()
    assert SW.warm_async(cfg) is True
    elapsed = time.monotonic() - t0
    assert elapsed < 0.25, f"warm_async blocked on the load ({elapsed:.2f}s)"
    assert SW._WORKER is None, "warm_async loaded synchronously"

    assert _wait_until(lambda: SW._WORKER is not None
                       and SW._WORKER.alive(), timeout=5), \
        "the background warm-up never loaded"
    assert SW._WORKER.key == SW._cfg_key(cfg)
    assert len(pidlog.read_text().split()) == 1


def test_warm_async_is_a_noop_when_the_worker_is_warm(
        tmp_path, fake_child, monkeypatch):
    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    cfg = _DataCfg()
    SW.synthesize_cancellable(cfg, "one", tmp_path / "a.wav",
                              stopped=lambda: False, timeout=10)
    warm_pid = SW._WORKER.proc.pid

    assert SW.warm_async(cfg) is False
    time.sleep(0.2)
    assert SW._WORKER is not None and SW._WORKER.proc.pid == warm_pid
    assert len(pidlog.read_text().split()) == 1, pidlog.read_text()


def test_warm_async_never_double_loads_while_loading(
        tmp_path, fake_child, monkeypatch):
    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    monkeypatch.setenv("FAKE_READY_S", "0.8")
    cfg = _DataCfg()

    assert SW.warm_async(cfg) is True
    assert SW.warm_async(cfg) is False, "a second load was queued"
    assert _wait_until(lambda: SW._WORKER is not None
                       and SW._WORKER.alive(), timeout=5)
    assert len(pidlog.read_text().split()) == 1, pidlog.read_text()


def test_warm_async_does_not_add_a_copy_beside_a_live_worker(
        tmp_path, fake_child, monkeypatch):
    """A warm worker of another identity owns the slot; no second copy."""
    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    cfg = _DataCfg()
    SW.synthesize_cancellable(cfg, "one", tmp_path / "a.wav",
                              stopped=lambda: False, timeout=10)
    other = _DataCfg(data={"tts": {"provider": "other"}})

    assert SW.warm_async(other) is False
    time.sleep(0.2)
    assert SW._WORKER is not None and SW._WORKER.key == SW._cfg_key(cfg)
    assert len(pidlog.read_text().split()) == 1, pidlog.read_text()


def test_a_reply_joins_an_inflight_warm_up_without_double_load(
        tmp_path, fake_child, monkeypatch):
    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    monkeypatch.setenv("FAKE_READY_S", "0.8")
    cfg = _DataCfg()

    assert SW.warm_async(cfg) is True
    provider = SW.synthesize_cancellable(cfg, "hello", tmp_path / "a.wav",
                                         stopped=lambda: False, timeout=10)
    assert provider == "fake"
    assert (tmp_path / "a.wav").read_bytes() == b"RIFFfake"
    assert len(pidlog.read_text().split()) == 1, pidlog.read_text()


def test_warm_async_never_raises_and_respects_the_opt_out(monkeypatch):
    assert SW.warm_async(object()) is False
    monkeypatch.setenv("QUBIT_TTS_SYNTH_WORKER", "0")
    assert SW.warm_async(_DataCfg()) is False
    monkeypatch.delenv("QUBIT_TTS_SYNTH_WORKER")

    def _boom(_cfg):
        raise RuntimeError("key failed")

    monkeypatch.setattr(SW, "_cfg_key", _boom)
    assert SW.warm_async(_DataCfg()) is False


def test_shutdown_discards_an_inflight_warm_up(tmp_path, fake_child,
                                               monkeypatch):
    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    monkeypatch.setenv("FAKE_READY_S", "0.6")
    assert SW.warm_async(_DataCfg()) is True
    SW.shutdown()
    time.sleep(0.9)
    assert SW._WORKER is None, "a warm-up installed after shutdown"


def test_warmed_worker_is_still_killable_on_stop(tmp_path, fake_child,
                                                 monkeypatch):
    monkeypatch.setenv("FAKE_PIDLOG", str(tmp_path / "pids.txt"))
    mode = tmp_path / "mode.txt"
    mode.write_text("ok")
    monkeypatch.setenv("FAKE_MODE_FILE", str(mode))
    cfg = _DataCfg()

    assert SW.warm_async(cfg) is True
    assert _wait_until(lambda: SW._WORKER is not None
                       and SW._WORKER.alive(), timeout=5)

    mode.write_text("sleep")
    stop = threading.Event()
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    t0 = time.monotonic()
    try:
        with pytest.raises(SW.SynthCancelled):
            SW.synthesize_cancellable(cfg, "long", tmp_path / "stop.wav",
                                      stopped=stop.is_set, timeout=20)
    finally:
        timer.cancel()
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"stop did not interrupt the warmed worker ({elapsed:.1f}s)"
    assert not (tmp_path / "stop.wav").exists()


# ---------------------------------------------------------------------------
# service hook: a KEYED ask starts the warm-up, an unkeyed one does not
# ---------------------------------------------------------------------------
def test_keyed_ask_warms_the_tts_worker(monkeypatch):
    calls = []
    monkeypatch.setattr(SW, "warm_async",
                        lambda cfg: calls.append(cfg) or True)
    svc = _svc(monkeypatch)
    monkeypatch.setattr(
        VoiceEditService, "_answer_question_text",
        lambda self, text, **kw: {"ok": True, "answer": "hi"})

    assert svc.ask("hello", request_id="rid-warm")["ok"] is True
    assert len(calls) == 1, "a keyed ask did not warm the worker"


def test_unkeyed_ask_does_not_warm_the_tts_worker(monkeypatch):
    calls = []
    monkeypatch.setattr(SW, "warm_async",
                        lambda cfg: calls.append(cfg) or True)
    svc = _svc(monkeypatch)
    monkeypatch.setattr(
        VoiceEditService, "_answer_question_text",
        lambda self, text, **kw: {"ok": True, "answer": "hi"})

    assert svc.ask("hello")["ok"] is True
    assert calls == [], "an unkeyed ask started a load"


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_stop_while_waiting_for_a_prewarm_stays_prompt(
        tmp_path, fake_child, monkeypatch):
    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    mode = tmp_path / "mode.txt"
    mode.write_text("ok")
    monkeypatch.setenv("FAKE_MODE_FILE", str(mode))
    cfg = _DataCfg()
    SW.synthesize_cancellable(cfg, "one", tmp_path / "a.wav",
                              stopped=lambda: False, timeout=10)
    monkeypatch.setenv("FAKE_READY_S", "1.5")
    _stop_during_synth(cfg, tmp_path / "stop.wav", mode)

    stop = threading.Event()
    timer = threading.Timer(0.15, stop.set)
    timer.start()
    t0 = time.monotonic()
    try:
        with pytest.raises(SW.SynthCancelled):
            SW.synthesize_cancellable(cfg, "next", tmp_path / "b.wav",
                                      stopped=stop.is_set, timeout=10)
    finally:
        timer.cancel()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.9, f"stop waited for the pre-warm ({elapsed:.2f}s)"
